#!/usr/bin/env ruby
# frozen_string_literal: true

require "json"
require "optparse"
require "pathname"
require "yaml"

ROOT = Pathname.new(File.expand_path("..", __dir__))
options = { event: nil, body: nil, base: nil, head: nil }
OptionParser.new do |o|
  o.banner = "Usage: ruby scripts/pr-policy.rb --event EVENT.json | --body FILE [--base SHA --head SHA]"
  o.on("--event PATH") { |v| options[:event] = v }
  o.on("--body PATH") { |v| options[:body] = v }
  o.on("--base SHA") { |v| options[:base] = v }
  o.on("--head SHA") { |v| options[:head] = v }
  o.on("-h", "--help") { puts o; exit 0 }
end.parse!
options[:event] ||= ENV["GITHUB_EVENT_PATH"] if options[:body].nil?

errors = []
body = ""
base_sha = options[:base]
head_sha = options[:head]

if options[:body]
  body = File.read(options[:body])
elsif options[:event]
  payload = JSON.parse(File.read(options[:event]))
  pr = payload.fetch("pull_request")
  body = pr["body"].to_s
  base_sha ||= pr.dig("base", "sha")
  head_sha ||= pr.dig("head", "sha")
else
  warn "--event or --body is required"
  exit 2
end

config = YAML.safe_load(ROOT.join("config/github-primitives.yaml").read, permitted_classes: [], aliases: false)
sections = Array(config["required_pull_request_sections"])
sections.each do |section|
  errors << "missing required section: ## #{section}" unless body.match?(/^##\s+#{Regexp.escape(section)}\s*$/i)
end

closing = body.scan(/\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?[ \t]+#(\d+)\b/i).flatten.map(&:to_i).uniq
errors << "PR body must link at least one issue with a closing keyword" if closing.empty?

lane = body[/^- Lane:\s*`?([^`\n]+)`?\s*$/i, 1]&.strip
contract = body[/^- Contract:\s*`?([^`\n]+)`?\s*$/i, 1]&.strip
errors << "lane ID is missing or still a placeholder" if lane.to_s.empty? || lane.include?("<!--")
errors << "lane contract path is missing or still a placeholder" if contract.to_s.empty? || contract.include?("<!--")
errors << "lane contract must be under .agent-workflow/sprints/.../lanes/contracts" unless contract.to_s.match?(%r{\A\.agent-workflow/sprints/[^/]+/lanes/contracts/[^/]+\.contract\.ya?ml\z})

reported_head = body[/^Current head SHA:\s*`?([0-9a-f]{40})`?\s*$/i, 1]
errors << "Current head SHA must be a 40-character commit SHA" unless reported_head
errors << "reported head SHA does not match the pull request head" if reported_head && head_sha && reported_head != head_sha
errors << "base and head SHA are identical" if base_sha && head_sha && base_sha == head_sha

if body.include?("<!--")
  errors << "pull request template still contains unresolved HTML placeholders"
end

if errors.empty?
  puts "Verdify pull request policy passed for issue(s): #{closing.map { |n| "##{n}" }.join(', ')}"
  exit 0
end
warn "Verdify pull request policy failed:"
errors.each { |e| warn "  - #{e}" }
exit 1
