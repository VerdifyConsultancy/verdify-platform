#!/usr/bin/env ruby
# frozen_string_literal: true

require "digest"
require "json"
require "pathname"
require "yaml"
require_relative "../lib/verdify"

ROOT = Pathname.new(File.expand_path("..", __dir__))
SKILL_NAME_PATTERN = /\A[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\z/
REQUIRED_SKILLS = %w[
  project-router transcript-replan northstar-research-ingest northstar-planning northstar-interview
  northstar-question-resolution project-definition architecture-contracts state-of-union repo-hygiene sprint-planning
  sprint-orchestrator controller-loop platform-readiness gravity-readiness lane-delivery
  independent-critic release-verification issue-triage
].freeze
REQUIRED_PR_SECTIONS = [
  "Backlog issue", "Lane contract", "Outcome", "Scope proof", "Evidence", "Risk and deployment impact"
].freeze

class RepoValidator
  attr_reader :errors, :warnings

  def initialize
    @errors = []
    @warnings = []
    @schema_ids = {}
  end

  def run
    validate_required_files
    validate_parseable_files
    validate_schemas
    validate_skills
    validate_host_links
    validate_workflow
    validate_github_templates
    validate_evaluations
    validate_scripts
    validate_examples
    validate_no_canonical_duplicates
    report
  end

  private

  def rel(path)
    Pathname.new(path).expand_path.relative_path_from(ROOT).to_s
  rescue ArgumentError
    path.to_s
  end

  def error(path, message)
    @errors << "#{rel(path)}: #{message}"
  end

  def warning(path, message)
    @warnings << "#{rel(path)}: #{message}"
  end

  def load_yaml(path)
    YAML.safe_load(Pathname.new(path).read, permitted_classes: [], aliases: false)
  rescue Psych::Exception => e
    error(path, "YAML parse failed: #{e.message}")
    nil
  end

  def load_json(path)
    JSON.parse(Pathname.new(path).read)
  rescue JSON::ParserError => e
    error(path, "JSON parse failed: #{e.message}")
    nil
  end

  def validate_required_files
    %w[
      README.md COMMON_OPERATING_CONTRACT.md AGENTS.md CLAUDE.md WORKFLOW.md AUTOMATION.md
      CONTRIBUTING.md SECURITY.md CHANGELOG.md VERSION Makefile verdify.workflow.yaml
      package.json npm/bin/verdify.js
      config/authority-matrix.yaml config/github-primitives.yaml config/lifecycle.yaml
      bin/verdify scripts/validate-repo.rb scripts/setup-agent-hosts.rb scripts/pr-policy.rb
      scripts/bootstrap-agent-session.sh scripts/verify-package.sh .github/pull_request_template.md
      .github/ISSUE_TEMPLATE/problem.yml .github/ISSUE_TEMPLATE/decision.yml
      .github/workflows/validate.yml .github/workflows/policy.yml
    ].each do |relative|
      path = ROOT.join(relative)
      error(path, "required file is missing") unless path.file?
    end
  end

  def validate_parseable_files
    Dir[ROOT.join("{config,schemas,.github,skills,examples}/**/*.{yaml,yml}")].sort.each { |p| load_yaml(p) }
    Dir[ROOT.join("{evaluations,examples}/**/*.json")].sort.each { |p| load_json(p) }
  end

  def validate_schemas
    paths = Dir[ROOT.join("schemas/*.schema.yaml")].sort.map { |p| Pathname.new(p) }
    error(ROOT.join("schemas"), "expected canonical schemas") if paths.empty?
    paths.each do |path|
      schema = load_yaml(path)
      next unless schema.is_a?(Hash)

      error(path, "must declare JSON Schema draft 2020-12") unless schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
      id = schema["$id"].to_s
      error(path, "missing $id") if id.empty?
      if !id.empty? && @schema_ids.key?(id)
        error(path, "duplicates $id from #{rel(@schema_ids[id])}")
      else
        @schema_ids[id] = path unless id.empty?
      end
      error(path, "top-level schema must be an object") unless schema["type"] == "object"
      error(path, "top-level schema must close unknown properties") unless schema["additionalProperties"] == false
      error(path, "schema_ref const should equal filename") unless schema.dig("properties", "schema_ref", "const") == path.basename.to_s
    end
  end

  def parse_frontmatter(path)
    content = File.binread(path).force_encoding(Encoding::UTF_8)
    unless content.valid_encoding?
      error(path, "is not valid UTF-8")
      return [{}, ""]
    end
    match = content.match(/\A---[ \t]*\n(.*?)\n---[ \t]*\n/m)
    unless match
      error(path, "missing YAML frontmatter")
      return [{}, content]
    end
    [YAML.safe_load(match[1], permitted_classes: [], aliases: false) || {}, content]
  rescue Psych::Exception => e
    error(path, "frontmatter parse failed: #{e.message}")
    [{}, ""]
  end

  def validate_skills
    actual = Dir[ROOT.join("skills/*/SKILL.md")].sort.map { |p| Pathname.new(p).dirname.basename.to_s }
    missing = REQUIRED_SKILLS - actual
    extra = actual - REQUIRED_SKILLS
    error(ROOT.join("skills"), "missing skills: #{missing.join(', ')}") unless missing.empty?
    error(ROOT.join("skills"), "unexpected top-level skills: #{extra.join(', ')}") unless extra.empty?

    REQUIRED_SKILLS.each do |name|
      dir = ROOT.join("skills", name)
      skill = dir.join("SKILL.md")
      next unless skill.file?
      frontmatter, content = parse_frontmatter(skill)
      fm_name = frontmatter["name"].to_s
      description = frontmatter["description"].to_s
      error(skill, "name must match directory") unless fm_name == name
      error(skill, "invalid skill name") unless fm_name.match?(SKILL_NAME_PATTERN) && !fm_name.include?("--") && fm_name.length <= 64
      error(skill, "description is required") if description.empty?
      error(skill, "description exceeds 1024 characters") if description.length > 1024
      error(skill, "description should state when the skill applies") unless description.match?(/\bUse\b/i)
      error(skill, "SKILL.md exceeds the progressive-disclosure limit of 500 lines") if content.lines.length > 500
      error(skill, "metadata.version must match VERSION") unless frontmatter.dig("metadata", "version").to_s == Verdify::VERSION
      error(skill, "missing agents/openai.yaml") unless dir.join("agents/openai.yaml").file?
      error(skill, "skill should have at least one reference") if Dir[dir.join("references/*")].empty?

      content.scan(/`((?:references|assets)\/[^`\s]+|\.\.\/\.\.\/(?:schemas|bin)\/[^`\s]+)`/).flatten.each do |raw|
        next if raw.include?("<") || raw.include?("*")
        clean = raw.sub(/[.,;:]\z/, "")
        target = dir.join(clean).cleanpath
        error(skill, "referenced path does not exist: #{clean}") unless target.exist?
      end

      metadata_path = dir.join("agents/openai.yaml")
      metadata = load_yaml(metadata_path)
      next unless metadata.is_a?(Hash)
      error(metadata_path, "interface.display_name is required") if metadata.dig("interface", "display_name").to_s.empty?
      short = metadata.dig("interface", "short_description").to_s
      error(metadata_path, "interface.short_description is required") if short.empty?
      error(metadata_path, "short_description exceeds 120 characters") if short.length > 120
      implicit = metadata.dig("policy", "allow_implicit_invocation")
      error(metadata_path, "allow_implicit_invocation must be boolean") unless [true, false].include?(implicit)
    end
  end

  def validate_host_links
    { ".agents/skills" => "Codex", ".claude/skills" => "Claude Code" }.each do |relative, host|
      REQUIRED_SKILLS.each do |name|
        link = ROOT.join(relative, name)
        source = ROOT.join("skills", name)
        unless link.symlink?
          error(link, "#{host} entry must be a symlink")
          next
        end
        begin
          error(link, "#{host} entry resolves outside canonical skill") unless link.realpath == source.realpath
        rescue Errno::ENOENT
          error(link, "#{host} entry is broken")
        end
      end
    end
  end

  def validate_workflow
    path = ROOT.join("verdify.workflow.yaml")
    workflow = load_yaml(path)
    return unless workflow.is_a?(Hash)

    stages = workflow["outline_stages"]
    unless stages.is_a?(Array) && stages.length == 17
      error(path, "outline_stages must contain exactly 17 original lifecycle stages")
      return
    end
    ids = stages.map { |stage| stage["id"] }
    error(path, "outline stage IDs must be 1..17") unless ids == (1..17).to_a
    stages.each do |stage|
      error(path, "stage #{stage['id']} references unknown skill #{stage['skill']}") unless REQUIRED_SKILLS.include?(stage["skill"])
      error(path, "stage #{stage['id']} has no mode") if stage["mode"].to_s.empty?
    end

    machine = workflow["state_machine"]
    states = machine.is_a?(Hash) ? machine["states"] : nil
    unless states.is_a?(Hash) && !states.empty?
      error(path, "state_machine.states is required")
      return
    end
    initial = machine["initial"]
    error(path, "initial state does not exist") unless states.key?(initial)
    states.each do |name, node|
      next unless node.is_a?(Hash)
      error(path, "state #{name} references unknown skill") if node["skill"] && !REQUIRED_SKILLS.include?(node["skill"])
      Array(node["transitions"]).each do |target|
        error(path, "state #{name} transitions to unknown state #{target}") unless states.key?(target)
      end
    end

    child = workflow.dig("lane_child_workflow", "states")
    if child.is_a?(Hash)
      child.each do |name, node|
        Array(node["next"]).each do |target|
          # PLAN_SPRINT is an intentional parent-workflow escape.
          next if target == "PLAN_SPRINT"
          error(path, "lane child state #{name} transitions to unknown state #{target}") unless child.key?(target)
        end
      end
    else
      error(path, "lane_child_workflow.states is required")
    end
  end

  def validate_github_templates
    problem = load_yaml(ROOT.join(".github/ISSUE_TEMPLATE/problem.yml"))
    if problem.is_a?(Hash)
      ids = Array(problem["body"]).map { |item| item["id"] if item.is_a?(Hash) }.compact
      %w[problem desired_outcome acceptance dependencies risk evidence].each do |id|
        error(ROOT.join(".github/ISSUE_TEMPLATE/problem.yml"), "missing field #{id}") unless ids.include?(id)
      end
    end

    template = ROOT.join(".github/pull_request_template.md")
    body = template.file? ? template.read : ""
    REQUIRED_PR_SECTIONS.each do |section|
      error(template, "missing required section ## #{section}") unless body.match?(/^##\s+#{Regexp.escape(section)}\s*$/i)
    end
    error(template, "must include a closing issue keyword") unless body.match?(/Closes\s+#/i)
    error(template, "must include current head SHA evidence") unless body.include?("Current head SHA")

    codeowners = ROOT.join(".github/CODEOWNERS")
    if codeowners.file? && codeowners.read.lines.any? { |line| line.strip.match?(/\A[^#].*@[\w-]+/) }
      warning(codeowners, "contains active owners; verify they are valid for the destination repository")
    end
  end

  def validate_evaluations
    REQUIRED_SKILLS.each do |skill|
      path = ROOT.join("evaluations", skill, "evals.json")
      unless path.file?
        error(path, "missing evaluation pack")
        next
      end
      data = load_json(path)
      next unless data.is_a?(Hash)
      error(path, "skill_name does not match") unless data["skill_name"] == skill
      evals = data["evals"]
      unless evals.is_a?(Array) && evals.length >= 2
        error(path, "must contain at least two evaluations")
        next
      end
      ids = evals.map { |item| item["id"] }
      error(path, "evaluation IDs must be unique") unless ids.uniq.length == ids.length
      evals.each do |item|
        %w[id prompt expected_output assertions].each do |key|
          error(path, "evaluation #{item['id'].inspect} missing #{key}") if item[key].nil? || (item[key].respond_to?(:empty?) && item[key].empty?)
        end
        error(path, "evaluation #{item['id']} should have at least three assertions") unless Array(item["assertions"]).length >= 3
      end
    end
  end

  def validate_scripts
    script_paths = %w[
      bin/verdify scripts/setup-agent-hosts.rb scripts/validate-repo.rb scripts/pr-policy.rb
      scripts/bootstrap-agent-session.sh scripts/launch-codex.sh scripts/launch-claude.sh scripts/package.sh scripts/verify-package.sh
      npm/bin/verdify.js tests/test_npm_install.sh
    ].map { |relative| ROOT.join(relative) }
    script_paths += Dir[ROOT.join("skills/*/scripts/*")].sort.map { |path| Pathname.new(path) }.select(&:file?)

    script_paths.each do |path|
      relative = rel(path)
      path = ROOT.join(relative)
      next unless path.file?
      error(path, "must be executable") unless path.executable?
      if path.extname == ".rb" || relative == "bin/verdify"
        error(path, "Ruby syntax check failed") unless system("ruby", "-c", path.to_s, out: File::NULL, err: File::NULL)
      elsif path.extname == ".sh"
        error(path, "Bash syntax check failed") unless system("bash", "-n", path.to_s, out: File::NULL, err: File::NULL)
      elsif path.extname == ".js"
        error(path, "JavaScript syntax check failed") unless system("node", "--check", path.to_s, out: File::NULL, err: File::NULL)
      end
    end
  end

  def validate_examples
    root = ROOT.join("examples/minimal-project/.agent-workflow")
    error(root, "complete example project is missing") unless root.directory?
    artifact_paths = Dir[root.join("**/*.{yaml,yml,json}")].sort.map { |p| Pathname.new(p) }
    artifact_paths.each do |path|
      document = path.extname == ".json" ? load_json(path) : load_yaml(path)
      next unless document.is_a?(Hash) && document["schema_ref"]
      ref = document["schema_ref"].to_s
      if ref.include?("/") || ref.include?("..")
        error(path, "schema_ref must be a schema filename")
        next
      end
      schema_path = ROOT.join("schemas", ref)
      unless schema_path.file?
        error(path, "schema_ref does not exist: #{ref}")
        next
      end
      Verdify::SchemaValidator.validate_file(path, schema_path).each { |message| error(path, message) }
      Verdify::SemanticValidator.validate(document).each { |message| error(path, message) }
    rescue Verdify::Error => e
      error(path, e.message)
    end

    validate_example_semantics(root)
  end

  def validate_example_semantics(root)
    plan_path = root.join("sprints/2026-06-22-a/sprint-plan.yaml")
    return unless plan_path.file?
    plan = load_yaml(plan_path)
    return unless plan.is_a?(Hash)
    if %w[approved active complete].include?(plan["status"])
      %w[issue_ids scope acceptance_criteria lanes].each do |field|
        error(plan_path, "approved sprint requires non-empty #{field}") if Array(plan[field]).empty?
      end
      if Array(plan.dig("review_plan", "user_stories_for_review")).empty?
        error(plan_path, "approved sprint requires review_plan.user_stories_for_review")
      end
      if Array(plan.dig("review_plan", "human_review_milestones")).empty?
        error(plan_path, "approved sprint requires review_plan.human_review_milestones")
      end
      error(plan_path, "approved sprint requires approved approval") unless plan.dig("approval", "status") == "approved"
    end

    assignments = Hash.new { |h, k| h[k] = [] }
    Array(plan["lanes"]).each do |lane|
      Array(lane["issue_ids"]).each { |issue| assignments[issue] << lane["lane_id"] }
      contract_path = root.parent.parent.join(lane["contract_path"].to_s.sub(/\A\.agent-workflow\//, ".agent-workflow/"))
      # The example root already points at .agent-workflow; normalize directly if needed.
      contract_path = root.join(lane["contract_path"].to_s.sub(/\A\.agent-workflow\//, "")) unless contract_path.file?
      error(plan_path, "lane contract missing: #{lane['contract_path']}") unless contract_path.file?
    end
    assignments.each do |issue, lanes|
      error(plan_path, "issue ##{issue} appears in multiple lanes: #{lanes.join(', ')}") if lanes.uniq.length > 1
    end

    contract_path = root.join("sprints/2026-06-22-a/lanes/contracts/issue-123-api.contract.yaml")
    closeout_path = root.join("sprints/2026-06-22-a/lanes/closeout/issue-123-api.closeout.yaml")
    critic_path = root.join("sprints/2026-06-22-a/critic/issue-123-api.critic.yaml")
    if contract_path.file?
      contract = load_yaml(contract_path)
      error(contract_path, "must enforce one coding session per worktree") unless contract.dig("worktree_policy", "one_coding_session_per_worktree") == true
      error(contract_path, "must require worktree lock") unless contract.dig("worktree_policy", "lock_required") == true
      if Array(contract["issue_ids"]).length > 1 && contract["coupling_justification"].to_s.strip.empty?
        error(contract_path, "multi-issue lane requires coupling justification")
      end
    end
    if closeout_path.file? && critic_path.file?
      closeout = load_yaml(closeout_path)
      critic = load_yaml(critic_path)
      error(critic_path, "critic session must differ from worker session") if critic["critic_session_id"] == closeout["worker_session_id"]
      error(critic_path, "critic must review the closeout head SHA") unless critic["reviewed_head_sha"] == closeout["head_sha"]
    end
  end

  def validate_no_canonical_duplicates
    candidates = Dir[ROOT.join("{schemas,skills}/**/*")].map { |p| Pathname.new(p) }.select(&:file?).reject(&:symlink?)
    groups = candidates.select { |p| p.size > 256 }.group_by { |p| Digest::SHA256.file(p).hexdigest }
    groups.each_value do |paths|
      next if paths.length < 2
      error(paths.first, "exact duplicate canonical content also appears at #{paths.drop(1).map { |p| rel(p) }.join(', ')}")
    end
  end

  def report
    unless warnings.empty?
      puts "Warnings:"
      warnings.sort.each { |item| puts "  - #{item}" }
    end
    unless errors.empty?
      puts "Errors:"
      errors.sort.each { |item| puts "  - #{item}" }
      exit 1
    end
    puts "Verdify repository validation passed (#{REQUIRED_SKILLS.length} skills, #{@schema_ids.length} schemas)."
  end
end

RepoValidator.new.run
