#!/usr/bin/env ruby
# frozen_string_literal: true

require "fileutils"
require "optparse"
require "pathname"

ROOT = Pathname.new(File.expand_path("..", __dir__))
SKILLS = Dir[ROOT.join("skills/*/SKILL.md")].sort.map { |p| Pathname.new(p).dirname.basename.to_s }

options = { root: ROOT, source: ROOT, check: false, hosts: %w[codex claude] }
OptionParser.new do |o|
  o.banner = "Usage: ruby scripts/setup-agent-hosts.rb [--check] [--root PATH] [--source PATH] [--host codex|claude|all]"
  o.on("--check", "Check links without modifying them") { options[:check] = true }
  o.on("--root PATH", "Repository where host links are installed") { |v| options[:root] = Pathname.new(v).expand_path }
  o.on("--source PATH", "Verdify package containing skills/") { |v| options[:source] = Pathname.new(v).expand_path }
  o.on("--host HOST", %w[codex claude all]) { |v| options[:hosts] = v == "all" ? %w[codex claude] : [v] }
  o.on("-h", "--help") { puts o; exit 0 }
end.parse!

HOST_DIRS = { "codex" => ".agents/skills", "claude" => ".claude/skills" }.freeze
errors = []

options[:hosts].each do |host|
  host_dir = options[:root].join(HOST_DIRS.fetch(host))
  FileUtils.mkdir_p(host_dir) unless options[:check]
  SKILLS.each do |skill|
    source = options[:source].join("skills", skill)
    link = host_dir.join(skill)
    unless source.join("SKILL.md").file?
      errors << "missing source skill #{source}"
      next
    end
    if options[:check]
      unless link.symlink?
        errors << "#{link} is not a symlink"
        next
      end
      begin
        errors << "#{link} resolves to #{link.realpath}, expected #{source.realpath}" unless link.realpath == source.realpath
      rescue Errno::ENOENT
        errors << "#{link} is broken"
      end
      next
    end

    if link.exist? || link.symlink?
      begin
        next if link.symlink? && link.realpath == source.realpath
      rescue Errno::ENOENT
        # replace broken link below
      end
      FileUtils.rm_rf(link)
    end
    relative = source.relative_path_from(link.dirname)
    File.symlink(relative, link)
    puts "linked #{link} -> #{relative}"
  end
end

unless errors.empty?
  warn errors.join("\n")
  exit 1
end
puts(options[:check] ? "Agent host links are valid." : "Agent host links installed.")
