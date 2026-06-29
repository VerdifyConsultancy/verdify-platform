#!/usr/bin/env ruby
# frozen_string_literal: true

require "digest"
require "json"
require "optparse"
require "pathname"
require "time"
require "yaml"

PROTECTED_PATTERNS = /
  public\s+api|schema|frontmatter|storage|retention|deletion|legal\s+hold|
  security|auth|authorization|identity|secret|credential|production|deploy|
  destructive|migration|external\s+dependency|provider|cost|network|rbac
/ix

QUESTION_MARKER = /\[(?:QUESTION|Q|NSQ|NQI)[:\-\s]+([A-Za-z0-9_.:-]+)\]\s*(.*)/
INLINE_ID = /\b((?:NSQ|NQI|HDG|ADR|RFC|Q)-[A-Za-z0-9_.:-]+)\b/

options = {
  root: nil,
  output: nil,
  format: "yaml",
  include_all_questions: false,
  context_chars: 180
}

OptionParser.new do |parser|
  parser.banner = "Usage: question_inventory.rb --root PATH [--output PATH] [--format yaml|json|markdown]"
  parser.on("--root PATH", "Corpus root to scan") { |value| options[:root] = value }
  parser.on("--output PATH", "Write output to path instead of stdout") { |value| options[:output] = value }
  parser.on("--format FORMAT", %w[yaml json markdown], "Output format") { |value| options[:format] = value }
  parser.on("--include-inferred", "Include non-marker lines containing question marks") { options[:include_all_questions] = true }
  parser.on("--context-chars N", Integer, "Context characters around inferred questions") { |value| options[:context_chars] = value }
  parser.on("-h", "--help") { puts parser; exit 0 }
end.parse!

abort "--root is required" unless options[:root]

root = Pathname.new(options[:root]).expand_path
abort "root does not exist: #{root}" unless root.directory?

def domain_for(relative)
  first = relative.to_s.split("/").first.to_s
  case first
  when /^00-product/ then "product"
  when /^01-architecture/ then "architecture"
  when /^02-contracts/ then "contracts"
  when /^03-data/ then "data"
  when /^04-pipelines/ then "pipelines"
  when /^05-modules/ then "modules"
  when /^06-apps/ then "apps-ux"
  when /^07-integrations/ then "integrations"
  when /^08-platform/ then "platform"
  when /^09-security/ then "security"
  when /^10-quality/ then "quality"
  when /^11-operations/ then "operations"
  when /^12-delivery/ then "delivery-agents"
  when /^13-decisions/ then "decisions"
  when /^14-research/ then "research"
  when /^99-reference/ then "reference"
  when "handover_context" then "handover"
  else first.empty? ? "root" : first
  end
end

def priority_for(text)
  return "P0" if text.match?(PROTECTED_PATTERNS)
  return "P1" if text.match?(/architecture|contract|interface|integration|pipeline|index|retrieval|provider/i)

  "P2"
end

def stable_id(relative, line_no, text, explicit)
  return explicit.gsub(/[^A-Za-z0-9_.:-]/, "-") if explicit && !explicit.empty?

  digest = Digest::SHA256.hexdigest("#{relative}:#{line_no}:#{text}")[0, 12]
  "Q-#{digest}"
end

def clean_question(text)
  text.to_s
      .sub(/^\s*[-*]\s*/, "")
      .sub(/^\s*#+\s*/, "")
      .strip
end

def candidate_questions(line, include_all)
  marker = line.match(QUESTION_MARKER)
  return [[marker[1], clean_question(marker[2].empty? ? line : marker[2]), "marker"]] if marker

  inline = line.match(INLINE_ID)
  if inline && line.include?("?")
    return [[inline[1], clean_question(line), "inline-id"]]
  end

  return [] unless include_all && line.include?("?")

  line.scan(/[^?]+\?/).map { |part| [nil, clean_question(part), "inferred"] }
end

questions = []
extensions = %w[.md .markdown .txt .yaml .yml]
root.find do |path|
  next unless path.file?
  next unless extensions.include?(path.extname.downcase)
  next if path.to_s.include?("/.git/")

  relative = path.relative_path_from(root)
  path.readlines(chomp: true).each_with_index do |line, index|
    candidate_questions(line, options[:include_all_questions]).each do |explicit_id, question, source_type|
      next if question.length < 8

      line_no = index + 1
      id = stable_id(relative, line_no, question, explicit_id)
      questions << {
        "id" => id,
        "source_type" => source_type,
        "question" => question,
        "file" => relative.to_s,
        "line" => line_no,
        "domain" => domain_for(relative),
        "priority" => priority_for(question),
        "protected_candidate" => question.match?(PROTECTED_PATTERNS),
        "context" => line.strip[0, options[:context_chars]]
      }
    end
  rescue ArgumentError, Encoding::InvalidByteSequenceError
    warn "Skipping non-text file #{path}"
  end
end

deduped = {}
questions.each do |question|
  key = [question["id"], question["file"], question["line"]]
  deduped[key] ||= question
end
questions = deduped.values.sort_by { |q| [q["domain"], q["file"], q["line"], q["id"]] }

domains = questions.group_by { |q| q["domain"] }.transform_values(&:length).sort.to_h
priorities = questions.group_by { |q| q["priority"] }.transform_values(&:length).sort.to_h
source_types = questions.group_by { |q| q["source_type"] }.transform_values(&:length).sort.to_h

inventory = {
  "schema_ref" => "northstar-question-inventory.v1",
  "kind" => "NorthStarQuestionInventory",
  "schema_version" => "1.0",
  "generated_at" => Time.now.utc.iso8601,
  "root" => root.to_s,
  "summary" => {
    "total_questions" => questions.length,
    "domains" => domains,
    "priorities" => priorities,
    "source_types" => source_types,
    "protected_candidates" => questions.count { |q| q["protected_candidate"] }
  },
  "questions" => questions
}

rendered = case options[:format]
           when "json"
             JSON.pretty_generate(inventory)
           when "markdown"
             lines = ["# Question Inventory", "", "- Root: `#{root}`", "- Total questions: #{questions.length}", ""]
             lines << "| ID | Priority | Domain | File | Line | Question |"
             lines << "| --- | --- | --- | --- | --- | --- |"
             questions.each do |q|
               text = q["question"].gsub("|", "\\|")
               lines << "| `#{q['id']}` | #{q['priority']} | #{q['domain']} | `#{q['file']}` | #{q['line']} | #{text} |"
             end
             lines.join("\n")
           else
             YAML.dump(inventory)
           end

if options[:output]
  output = Pathname.new(options[:output]).expand_path
  output.dirname.mkpath
  output.write(rendered)
else
  puts rendered
end
